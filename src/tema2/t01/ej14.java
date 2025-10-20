package tema2.t01;

import java.util.Scanner;

public class ej14 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        int numero;

        do {
            System.out.println("Digite un numero:");
            numero=sc.nextInt();
        } while (numero < 0);

        System.out.println("El numero introducido es: " + numero);
    }

}
