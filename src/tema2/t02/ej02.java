package tema2.t02;

import java.util.Scanner;

public class ej02 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);
        int contador = 0;

        System.out.println("Digite el numero base");
        int base = sc.nextInt();
        System.out.println("Digite el exponente");
        int expo = sc.nextInt();

        for (int i = 1; i <= expo; i++){
            contador = base * base;
        }
        System.out.println(base + " Elevado a " + expo + " es: " + contador);
}
}
