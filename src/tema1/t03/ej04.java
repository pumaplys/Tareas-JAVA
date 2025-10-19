package tema1.t03;

import java.util.Scanner;

public class ej04 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Digite un numero entero:");
        int numero = sc.nextInt();

        System.out.println(numero + " es " + ((numero % 7 == 0) ? "multiplo de 7" : "otro multiplo"));
    }
}
