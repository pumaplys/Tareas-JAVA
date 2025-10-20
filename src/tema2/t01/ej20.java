package tema2.t01;

import java.util.Scanner;

public class ej20 {
    public static void main(String args[]) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Digite un numero:");
        int num = sc.nextInt();

        int suma = 0;

        for (int i = 1; i <= 100; i++){
            suma += num + i;
        }
        System.out.println("La suma de los 100 numeros siguientes de " + num + " es: " + suma);
    }
}
